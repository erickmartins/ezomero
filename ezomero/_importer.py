import logging
import tempfile
import yaml
from typing import Optional, Union, List
from os.path import abspath
from os import unlink
from omero.gateway import MapAnnotationWrapper, BlitzGateway
from ._gets import get_image_ids
from ._posts import post_dataset, post_project, post_screen
from ._misc import link_images_to_dataset
from ._misc import link_plates_to_screen
from omero.cli import CLI
from omero.plugins.sessions import SessionsControl
from importlib import import_module
ImportControl = import_module("omero.plugins.import").ImportControl


# import
def ezimport(conn: BlitzGateway, target: str,
             project: Optional[Union[str, int]] = None,
             dataset: Optional[Union[str, int]] = None,
             screen: Optional[Union[str, int]] = None,
             ann: Optional[dict] = None,
             ns: Optional[str] = None, *args: str,
             **kwargs: str
             ) -> Union[List[int], None]:
    """Entry point that creates Importer and runs import.

    Parameters
    ----------
    conn : ``omero.gateway.BlitzGateway`` object.
        OMERO connection.
    target : string
        Path to the import target to be imported into OMERO.
    project : str or int, optional
        The name or ID of the Project data will be imported into.
    dataset : str or int, optional
        The name or ID of the Dataset data will be imported into.
    screen : str or int, optional
        The name or ID of the Screen data will be imported into.
    ann : dict, optional
        Dictionary with key-value pairs to be added to imported images.
    ns : str, optional
        Namespace for the added key-value pairs.
    *args, **kwargs : str, optional
        You can also add any extra arguments you would like to pass to
        ``omero import`` to the end of the argument list in ``ezimport``.
        For example, an in-place import can be done by adding
        ``transfer="ln_s"`` as an extra argument when calling `ezimport``.

    Returns
    -------
    plate_ids or image_ids : list of ints
        The ids of the Images/Plates that were generated by importing the
        specified target.

    Notes
    -------
    This function is EXPERIMENTAL and has seen minimal testing. Use at
    your own risk! We do not recommend using this in production.
    """

    imp_ctl = Importer(conn, target, project, dataset, screen,
                       ann, ns, *args, **kwargs)
    rv = imp_ctl.ezimport()
    if rv:
        if imp_ctl.screen:
            imp_ctl.get_plate_ids()
            imp_ctl.organize_plates()
            imp_ctl.annotate_plates()
            return imp_ctl.plate_ids

        else:
            imp_ctl.get_my_image_ids()
            imp_ctl.organize_images()
            imp_ctl.annotate_images()
            return imp_ctl.image_ids


def set_or_create_project(conn: BlitzGateway, project: Union[str, int],
                          across_groups: Optional[bool] = True) -> int:
    """Create or set a Project of interest.

    If argument is a string, creates a new Project with that name. If it is
    an integer, sets that Project ID as the Project of interest.
    Parameter
    ---------
    conn : ``omero.gateway.BlitzGateway`` object.
        OMERO connection.
    project : str or int
        The name or ID of the Project needed.
    Returns
    -------
    project_id : int
        The id of the Project that was either found or created.
    """
    if isinstance(project, str):
        project_id = post_project(conn, project)
        print(f'Created new Project:{project_id}')
    elif (isinstance(project, int)):
        project_id = project
    else:
        raise TypeError("'project' must be str or int")
    return project_id


def set_or_create_dataset(conn: BlitzGateway, project_id: Union[int, None],
                          dataset: Union[str, int],
                          across_groups: Optional[bool] = True
                          ) -> Union[int, None]:
    """Create or set a Dataset of interest.

    If argument is a string, creates a new Dataset with that name. If it is
    an integer, sets that Dataset ID as the Dataset of interest. If
    ``project_id`` is specified, new Dataset will be created in that Project.
    Parameter
    ---------
    conn : ``omero.gateway.BlitzGateway`` object.
        OMERO connection.
    project_id : int
        Id of Project in which to find/create Dataset.
    dataset : str
        The name or ID of the Dataset needed.
    Returns
    -------
    dataset_id : int
        The id of the Dataset that was either found or created.
    """
    if isinstance(dataset, str):
        if project_id:
            dataset_id = post_dataset(conn, dataset, project_id=project_id)
        else:
            dataset_id = post_dataset(conn, dataset)
        print(f'Created new Dataset:{dataset_id}')
    elif (isinstance(dataset, int)):
        dataset_id = dataset
    else:
        raise TypeError("'dataset' must be str or int")
    return dataset_id


def set_or_create_screen(conn: BlitzGateway, screen: Union[str, int],
                         across_groups: Optional[bool] = True) -> int:
    """Create or set a Screen of interest.

    If argument is a string, creates a new Screen with that name. If it is
    an integer, sets that Screen ID as the Screen of interest.
    Parameter
    ---------
    conn : ``omero.gateway.BlitzGateway`` object.
        OMERO connection.
    screen : str or int
        The name or ID of the Screen needed.
    Returns
    -------
    screen_id : int
        The id of the Screen that was either found or created.
    """
    if isinstance(screen, str):
        screen_id = post_screen(conn, screen)
        print(f'Created new screen:{screen_id}')
    elif (isinstance(screen, int)):
        screen_id = screen
    else:
        raise TypeError("'screen' must be str or int")
    return screen_id


def multi_post_map_annotation(conn: BlitzGateway, object_type: str,
                              object_ids: Union[int, List[int]], kv_dict: dict,
                              ns: str) -> int:
    """Create a single new MapAnnotation and link to multiple images.
    Parameters
    ----------
    conn : ``omero.gateway.BlitzGateway`` object
        OMERO connection.
    object_type : str
       OMERO object type, passed to ``BlitzGateway.getObjects``
    object_ids : int or list of ints
        IDs of objects to which the new MapAnnotation will be linked.
    kv_dict : dict
        key-value pairs that will be included in the MapAnnotation
    ns : str
        Namespace for the MapAnnotation
    Notes
    -----
    All keys and values are converted to strings before saving in OMERO.
    Returns
    -------
    map_ann_id : int
        IDs of newly created MapAnnotation
    Examples
    --------
    >>> ns = 'jax.org/jax/example/namespace'
    >>> d = {'species': 'human',
             'occupation': ['time traveler', 'soldier'],
             'first name': 'Kyle',
             'surname': 'Reese'}
    >>> multi_post_map_annotation(conn, "Image", [23,56,78], d, ns)
    234
    """
    if type(object_ids) not in [list, int]:
        raise TypeError('object_ids must be list or integer')
    if isinstance(object_ids, int):
        object_ids = [object_ids]

    if len(object_ids) == 0:
        raise ValueError('object_ids must contain one or more items')

    if type(kv_dict) is not dict:
        raise TypeError('Annotation must be of type `dict`')

    kv_pairs = []
    for k, v in kv_dict.items():
        k = str(k)
        if type(v) is not list:
            v = str(v)
            kv_pairs.append([k, v])
        else:
            for value in v:
                value = str(value)
                kv_pairs.append([k, value])

    map_ann = MapAnnotationWrapper(conn)
    map_ann.setNs(str(ns))
    map_ann.setValue(kv_pairs)
    map_ann.save()
    for o in conn.getObjects(object_type, object_ids):
        o.linkAnnotation(map_ann)
    return map_ann.getId()


class Importer:
    """Class for managing OMERO imports using OMERO CLI.

    Parameters
    ----------
    conn : ``omero.gateway.BlitzGateway`` object.
        OMERO connection.
    file_path : string
        Path to the import target to be imported into OMERO.
    project : str or int, optional
        The name or ID of the Project data will be imported into.
    dataset : str or int, optional
        The name or ID of the Dataset data will be imported into.
    screen : str or int, optional
        The name or ID of the Screen data will be imported into.
    ann : dict, optional
        Dictionary with key-value pairs to be added to imported images.
    ns : str, optional
        Namespace for the added key-value pairs.
    host : str, optional
        Hostname of the OMERO server to which data will be imported.
    port : int, optional
        Port of the OMERO server to which data will be imported.
    *args, **kwargs : str, optional
        Receives the ``*args``, ``**kwargs`` from ``ezimport`` to pass it
        onto ``omero import``.

    Important notes:
    1) Setting ``project`` also requires setting ``dataset``. Failing to do so
    will raise a ValueError.
    2) To annotate images, both ``ann`` and ``ns`` need to be set. If one of
    them is not set, no annotations will be made.
    3) For automating purposes, the arguments ``host`` and ``port`` can be set,
    avoiding a user prompt for that info. Both need to be set to bypass that
    prompt.
    4) Due to the method we use for detecting imported image IDs, passing
    through the `--file` argument to redirect the stdout output of `omero
    import`is not possible - `--err` to redirect the stderr output should
    be possible.
    """

    def __init__(self, conn: BlitzGateway, file_path: str,
                 project: Optional[Union[str, int]],
                 dataset: Optional[Union[str, int]],
                 screen: Optional[Union[str, int]],
                 ann: Optional[dict],
                 ns: Optional[str], *args, **kwargs):
        self.conn = conn
        self.file_path = abspath(file_path)
        self.session_uuid = conn.getSession().getUuid().val
        self.project = project
        self.dataset = dataset
        self.common_args = args
        self.named_args = kwargs
        self.import_result = ""

        if self.project and not self.dataset:
            raise ValueError("Cannot define project but no dataset!")
        self.screen = screen
        self.imported = False
        self.image_ids: Union[List[int], None] = None
        self.plate_ids: Union[List[int], None] = None
        self.ann = ann
        self.ns = ns

    def get_my_image_ids(self) -> Union[List[int], None]:
        """Get the Ids of imported images.

        Note that this will not find images if they have not been imported.
        Also, while image_ids are returned, this method also sets
        ``self.image_ids``.
        Returns
        -------
        image_ids : list of ints
            Ids of images imported from the specified client path, which
            itself is derived from ``self.file_path`` and ``self.filename``.
        """
        self.image_ids = self.import_result[0]['Image']
        return self.image_ids

    def make_substitutions(self) -> str:
        fpath = self.file_path
        mytable = fpath.maketrans("\"*:<>?\\|", "\'x;[]%/!")
        final_path = fpath.translate(mytable)
        return final_path

    def get_plate_ids(self) -> Union[List[int], None]:
        """Get the Ids of imported plates.
        Note that this will not find plates if they have not been imported.
        Also, while plate_ids are returned, this method also sets
        ``self.plate_ids``.
        Returns
        -------
        plate_ids : list of ints
            Ids of plates imported from the specified client path, which
            itself is derived from ``self.file_path`` and ``self.filename``.
        """
        self.plate_ids = self.import_result[0]['Plate']
        return self.plate_ids

    def annotate_images(self) -> Union[int, None]:
        """Post map annotation (``self.ann``) to images ``self.image_ids``.
        Returns
        -------
        map_ann_id : int
            The Id of the MapAnnotation that was created.
        """
        if not self.ann or not self.ns:
            logging.warning("Missing annotation or namespace, "
                            "skipping annotations")
            return None
        if self.image_ids:
            if len(self.image_ids) == 0:
                logging.error('No image ids to annotate')
                return None
            else:
                map_ann_id = multi_post_map_annotation(self.conn, "Image",
                                                       self.image_ids,
                                                       self.ann,
                                                       self.ns)
                return map_ann_id
        return None

    def annotate_plates(self) -> Union[int, None]:
        """Post map annotation (``self.ann``) to plates ``self.plate_ids``.
        Returns
        -------
        map_ann_id : int
            The Id of the MapAnnotation that was created.
        """
        if not self.ann or not self.ns:
            logging.warning("Missing annotation or namespace, "
                            "skipping annotations")
            return None
        if self.plate_ids:
            if len(self.plate_ids) == 0:
                logging.error('No plate ids to annotate')
                return None
            else:
                map_ann_id = multi_post_map_annotation(self.conn, "Plate",
                                                       self.plate_ids,
                                                       self.ann,
                                                       self.ns)
                return map_ann_id
        return None

    def organize_images(self) -> bool:
        """Move images to ``self.project``/``self.dataset``.
        Returns
        -------
        image_moved : boolean
            True if images were found and moved, else False.
        """
        if not self.image_ids:
            logging.error('No image ids to organize')
            return False
        orphans = get_image_ids(self.conn)
        if self.project:
            project_id = set_or_create_project(self.conn,
                                               self.project)
        else:
            project_id = None
        if self.dataset:
            dataset_id = set_or_create_dataset(self.conn,
                                               project_id,
                                               self.dataset)
        else:
            dataset_id = None
        for im_id in self.image_ids:
            if im_id not in orphans:
                logging.error(f'Image:{im_id} not an orphan')
            else:
                if dataset_id:
                    link_images_to_dataset(self.conn, [im_id], dataset_id)
                    print(f'Moved Image:{im_id} to Dataset:{dataset_id}')
        return True

    def organize_plates(self) -> bool:
        """Move plates to ``self.screen``.
        Returns
        -------
        plate_moved : boolean
            True if plates were found and moved, else False.
        """
        if self.plate_ids:
            if len(self.plate_ids) == 0:
                logging.error('No plate ids to organize')
                return False
            for pl_id in self.plate_ids:
                if self.screen:
                    screen_id = set_or_create_screen(self.conn, self.screen)
                    link_plates_to_screen(self.conn, [pl_id], screen_id)
                    print(f'Moved Plate:{pl_id} to Screen:{screen_id}')
            return True
        return False

    def ezimport(self) -> bool:
        """Import file.
        Returns
        -------
        import_status : boolean
            True if OMERO import returns a 0 exit status, else False.
        """
        args = ""
        if self.common_args:
            args = args + " ".join(self.common_args)
        if self.named_args:
            for k, v in self.named_args.items():
                args = args + " " + str(k) + "=" + str(v)
        cli = CLI()
        cli.register('import', ImportControl, '_')
        cli.register('sessions', SessionsControl, '_')
        stdout_file = tempfile.NamedTemporaryFile(mode="r", delete=False)
        stdout_file.close()
        arguments = ['import',
                     '-k', self.conn.getSession().getUuid().val,
                     '-s', self.conn.host,
                     '-p', str(self.conn.port),
                     ]
        if self.common_args:
            str_args = ['--{}'.format(v) for v in self.common_args]
            arguments.extend(str_args)
        if self.named_args:
            str_kwargs = ['--{}={}'.format(k, v) for k, v in
                          self.named_args.items()]
            arguments.extend(str_kwargs)
        arguments.extend(['--file', stdout_file.name, '--output', 'yaml'])
        arguments.append(str(self.file_path))
        cli.invoke(arguments)
        with open(stdout_file.name, 'r') as f:
            self.import_result = yaml.safe_load(f)
        unlink(stdout_file.name)
        if self.import_result:
            self.imported = True
            print(f'Imported {self.file_path}')
            return True
        else:
            logging.error(f'Import of {self.file_path} has failed!')
            return False
